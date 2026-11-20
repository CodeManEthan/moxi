"""Records to rows: which records sessions hands over become index rows, and how.

memory parses no harness format. Every record comes from `moxi_sessions.api`, and this
module only decides which of them count and what text each row holds.

**Tier 1** (`Row`, the `rows` table) holds what a person or an agent wrote in the session:

- each message whose role is `user` or `assistant`, that is not a thought (`reasoning`),
  that sessions doesn't mark `injected`, that the harness doesn't flag as meta, and that
  is not a compaction summary. A message sessions marks `injected` as unknown is kept;
- each tool call, as its summary line and its input.

**Tier 2** (`Tail`, the `tails` table) holds the end of each main-session tool result,
`tail_chars` characters long, with whether the tool failed. A subagent's results give no
tail.

**Redaction, then the cut.** Every text is redacted with `moxi_secrets.redact` and the
configured sets before it is cut to its size, so a cut doesn't leave part of a secret
whose shape no longer matches. A long text is redacted a window at a time, and the window
grows until its redacted form runs at least `MARGIN` characters past the cut, so only a
secret longer than `MARGIN` that straddles the window's edge could leave a part in what is
kept. A text secrets reports it could not scan is stored as its placeholder alone. A
message keeps its first `MESSAGE_CHARS` characters and a tool call its first `TOOL_CHARS`;
a tail keeps its last `tail_chars`. Each cut text says how many characters went.

**A stored cut, trimmed.** A rebuild that redacts again the rows stored under an older
`redact` (`moxi_secrets.REDACT_VERSION`) copies the rows of a source it can't read again,
and a secret the older `redact` let through could straddle such a row's cut, leaving a
fragment no rule matches. `recut_head` and `recut_tail` with `edge_chars` take off, before
they redact again, the text from the stored cut to the first whitespace at least
`edge_chars` characters away, or everything to the text's other end when there is none.
They read past the characters secrets sees through (`moxi_secrets.scan.seen_through`), as
the text still holds them: those count nothing toward `edge_chars`, and none ends the span,
so a control character Python reads as whitespace can't stop it inside a token it splits.

**The secret-path rule.** A tool call is a secret-path call when any whitespace-separated
word of any string in its input is a path `moxi_secrets.is_secret_path` names; strings
inside lists and nested objects count, and so does a string that holds a JSON object or
list. Such a call keeps one line, the tool's name and the path, and loses its input; its
result gives no tail. The path shown is the matched word alone, never a run of text with
whitespace in it, so a heredoc or a patch into a secret file doesn't reach the index.

**The key.** A row's key names one record: the harness, then the line's own id where the
harness writes one (Claude Code's `uuid`, OpenCode's row id) or else the source and the
line's place in it, then the record's place among the records sessions gives for that
line, counted from 0. The session id is not part of it, so a line a resumed Claude Code
session copied from its parent lands once.

Standard library at import; `moxi_secrets` is imported at the first redaction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

#: The record kinds a read asks sessions for. A record's place among its line's records
#: is counted among these kinds alone, so every read must ask for the same ones.
KINDS = ("message", "tool_call", "tool_result")

#: How many characters of a message tier 1 keeps.
MESSAGE_CHARS = 16384
#: How many characters of a tool call (its summary line and input) tier 1 keeps.
TOOL_CHARS = 2048
#: How far past the part it keeps a text is redacted, counted in the redacted text: the
#: window redacted grows until its redacted form is this much longer than the part kept,
#: or holds the whole text. Only a secret longer than this that straddles the window's
#: edge could leave a part of itself in the kept part.
MARGIN = 65536

#: How far a trim of a stored cut reaches, in characters other than those secrets sees
#: through: from the cut to the first whitespace at least this far away.
EDGE_CHARS = 64

#: A row's kind for a tool call; a message's row takes its role.
TOOL = "tool"
#: The placeholder secrets writes for a text it could not scan.
UNSCANNED = "[REDACTED:unscanned]"

#: The characters stripped from each end of a word before it is tested as a path.
_PUNCTUATION = "\"'`()[]{}<>,;:|&"
#: The characters a word is split at, so that a path joined to an assignment, a shell
#: redirection or other text (`--env-file=.env`, `cat<.env`, `x>/a/.env`,
#: `{"path":"/a/.env","content":"A=1"}`) is tested, and shown, alone.
_JOINS = re.compile(r"[=<>|;&,:\"'`()\[\]{}\x00]+")


@dataclass(frozen=True)
class Policy:
    """How rows are made: the secrets sets every text is redacted with, and how many
    characters of a tool result tier 2 keeps."""

    sets: tuple[str, ...]
    tail_chars: int


@dataclass(frozen=True)
class Row:
    """One tier 1 row. `session_id` is the main session's id; `agent_id` the subagent's
    own id, None on a main session's row. `kind` is the message's role or `tool`."""

    key: str
    harness: str
    session_id: str
    agent_id: str | None
    label: str | None
    time: float | None
    turn_id: str | None
    kind: str
    text: str


@dataclass(frozen=True)
class Tail:
    """One tier 2 row: the end of one main-session tool result."""

    key: str
    harness: str
    session_id: str
    label: str | None
    time: float | None
    turn_id: str | None
    failed: bool
    text: str


@dataclass
class Converted:
    """What one batch of records gave: the rows, the tails, the call ids found to be
    secret-path calls, and the newest record time (None when no record had one)."""

    rows: list[Row] = field(default_factory=list)
    tails: list[Tail] = field(default_factory=list)
    secret_calls: set[str] = field(default_factory=set)
    newest: float | None = None


# -- redaction and the cut ------------------------------------------------------------

def redact_version() -> int:
    """Which `redact` the installed secrets is: its `REDACT_VERSION`, or 1 for a secrets
    without one."""
    import moxi_secrets

    version = getattr(moxi_secrets, "REDACT_VERSION", 1)
    return version if isinstance(version, int) and not isinstance(version, bool) else 1


def redact(text: str, sets: Iterable[str]) -> str:
    """`text` redacted with `sets`, or the unscanned placeholder alone when secrets
    could not scan it."""
    import moxi_secrets

    done = moxi_secrets.redact(text, tuple(sets))
    if UNSCANNED in done and UNSCANNED not in text:
        return UNSCANNED
    return done


def _redact_window(text: str, limit: int, sets: tuple[str, ...],
                   *, tail: bool) -> tuple[str, int]:
    """The redacted window of `text` that a cut to `limit` characters takes from, and how
    many characters of `text` lie outside it.

    The window starts `MARGIN` characters past `limit` and doubles while its redacted
    form is shorter than `limit + MARGIN` and text remains, so the cut edge stays at
    least `MARGIN` redacted characters inside the window's edge however much redaction
    shrank the window."""
    want = limit + MARGIN
    size = want
    while True:
        if size >= len(text):
            return redact(text, sets), 0
        part = text[-size:] if tail else text[:size]
        clean = redact(part, sets)
        if clean == UNSCANNED or len(clean) >= want:
            return clean, len(text) - size
        size *= 2


def keep_head(text: str, limit: int, sets: Iterable[str]) -> str:
    """`text` redacted, then cut to its first `limit` characters with a marker that says
    how many went: the redacted characters past the cut, and the characters past the
    redacted window."""
    clean, beyond = _redact_window(text, limit, tuple(sets), tail=False)
    if clean == UNSCANNED:
        return clean
    gone = len(clean) + beyond - limit
    if gone <= 0:
        return clean
    return f"{clean[:limit]} [... {gone} more characters cut]"


def keep_tail(text: str, limit: int, sets: Iterable[str]) -> str:
    """`text` redacted, then cut to its last `limit` characters with a marker before
    them that says how many went, counted as `keep_head` counts them."""
    clean, before = _redact_window(text, limit, tuple(sets), tail=True)
    if clean == UNSCANNED:
        return clean
    gone = len(clean) + before - limit
    if gone <= 0:
        return clean
    return f"[... {gone} earlier characters cut] {clean[-limit:]}"


_HEAD_CUT = re.compile(r"(.*) \[\.\.\. ([0-9]+) more characters cut\]", re.S)
_TAIL_CUT = re.compile(r"\[\.\.\. ([0-9]+) earlier characters cut\] (.*)", re.S)


def _edge_head(body: str, edge_chars: int) -> int:
    """Where a head-cut body's kept part ends once its cut edge is trimmed: at the first
    whitespace, back from the end, with at least `edge_chars` counted characters after
    it, or 0 with none."""
    from moxi_secrets.scan import seen_through

    counted = 0
    for position in range(len(body) - 1, -1, -1):
        c = body[position]
        if seen_through(c):
            continue
        if counted >= edge_chars and c.isspace():
            return position
        counted += 1
    return 0


def _edge_tail(body: str, edge_chars: int) -> int:
    """Where a tail-cut body's kept part starts once its cut edge is trimmed: past the
    first whitespace with at least `edge_chars` counted characters before it, or the
    body's end with none."""
    from moxi_secrets.scan import seen_through

    counted = 0
    for position, c in enumerate(body):
        if seen_through(c):
            continue
        if counted >= edge_chars and c.isspace():
            return position + 1
        counted += 1
    return len(body)


def recut_head(text: str, limit: int, sets: Iterable[str], *, edge_chars: int = 0) -> str:
    """A stored head-cut text redacted again with `sets` and cut to `limit`, its marker
    counting what an earlier cut took too. For a row a rebuild copies without its
    record. With `edge_chars`, a text with a stored cut first loses its cut edge (see
    the module's "A stored cut, trimmed"), and the marker counts that too."""
    match = _HEAD_CUT.fullmatch(text)
    body, gone = (match.group(1), int(match.group(2))) if match else (text, 0)
    if match and edge_chars:
        end = _edge_head(body, edge_chars)
        body, gone = body[:end], gone + len(body) - end
    clean = redact(body, tuple(sets))
    if clean == UNSCANNED:
        return clean
    gone += max(0, len(clean) - limit)
    return f"{clean[:limit]} [... {gone} more characters cut]" if gone else clean


def recut_tail(text: str, limit: int, sets: Iterable[str], *, edge_chars: int = 0) -> str:
    """A stored tail redacted again with `sets` and cut to its last `limit` characters,
    as `recut_head` does at the head, its cut edge trimmed first with `edge_chars`."""
    match = _TAIL_CUT.fullmatch(text)
    body, gone = (match.group(2), int(match.group(1))) if match else (text, 0)
    if match and edge_chars:
        start = _edge_tail(body, edge_chars)
        body, gone = body[start:], gone + start
    clean = redact(body, tuple(sets))
    if clean == UNSCANNED:
        return clean
    gone += max(0, len(clean) - limit)
    return f"[... {gone} earlier characters cut] {clean[-limit:]}" if gone else clean


# -- the secret-path rule --------------------------------------------------------------

@lru_cache(maxsize=65536)
def _is_secret(word: str) -> bool:
    import moxi_secrets

    return moxi_secrets.is_secret_path(word)


def _candidates(text: str) -> Iterable[str]:
    """The words of `text` to test as paths, narrowest first: for each
    whitespace-separated word, the pieces between the characters of `_JOINS`, then
    the word with quotes and brackets taken off its ends, then the word itself. None of
    them holds whitespace, so the path a call is shown with is never more than one word."""
    for word in text.split():
        bare = word.strip(_PUNCTUATION)
        pieces = [p.strip(_PUNCTUATION) for p in _JOINS.split(bare)]
        if len(pieces) > 1:
            yield from (p for p in pieces if p)
        if bare != word:
            yield bare
        yield word


def secret_path(value: Any, _depth: int = 0) -> str | None:
    """The first path in a tool's input that secrets says must not be read, or None.

    Each whitespace-separated word of every string counts, inside lists and nested
    objects too; a string that holds a JSON object or list is read into and searched the
    same way. What comes back is one word, never a string with whitespace in it."""
    if _depth > 32:
        return None
    if isinstance(value, str):
        for word in _candidates(value):
            if word and _is_secret(word):
                return word
        head = value.lstrip()[:1]
        if head in ("{", "["):
            try:
                inner = json.loads(value)
            except ValueError:
                return None
            if isinstance(inner, (dict, list)):
                return secret_path(inner, _depth + 1)
        return None
    if isinstance(value, Mapping):
        values: Iterable[Any] = value.values()
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return None
    for item in values:
        found = secret_path(item, _depth + 1)
        if found is not None:
            return found
    return None


# -- the key ---------------------------------------------------------------------------

def line_id(record: Any, source_key: str) -> str:
    """The id of the line or row a record came from: the harness's own id for it where
    the harness writes one, else the source and the line's place in it."""
    if record.harness == "claude":
        uuid = record.extra.get("uuid") if isinstance(record.extra, Mapping) else None
        if isinstance(uuid, str) and uuid:
            return f"uuid:{uuid}"
    at = record.at if isinstance(record.at, Mapping) else {}
    table, rid = at.get("table"), at.get("id")
    if isinstance(table, str) and rid is not None:
        return f"{table}:{rid}"
    line = at.get("line")
    if isinstance(line, int) and not isinstance(line, bool):
        return f"{source_key}@line:{line}"
    return f"{source_key}@offset:{at.get('offset')}"


def keys(records: Iterable[Any], source_key: str) -> list[str]:
    """Each record's key, in order: the harness, its line's id, and its place among the
    records of that line in this list, counted from 0."""
    places: dict[str, int] = {}
    out = []
    for record in records:
        lid = line_id(record, source_key)
        place = places.get(lid, 0)
        places[lid] = place + 1
        out.append(f"{record.harness}|{lid}|{place}")
    return out


# -- records to rows -------------------------------------------------------------------

def _known(value: Any) -> Any:
    """`value`, or None for the sessions' unknown sentinel and anything that is not a
    plain string or number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        return value
    return None


def tier_one_message(record: Any) -> bool:
    """Whether a message record belongs in tier 1."""
    if record.role not in ("user", "assistant") or record.reasoning:
        return False
    if record.injected is True:
        return False
    flags = record.flags if isinstance(record.flags, Mapping) else {}
    if flags.get("isMeta") is True or flags.get("isCompactSummary") is True:
        return False
    if flags.get("summary") is True:     # a summary the harness wrote of the session
        return False
    return bool(record.text and record.text.strip())


def model_answer(record: Any) -> bool:
    """Whether a record is the model's own answer: a tool call, or an assistant message
    that is not a thought and that the harness didn't make up."""
    if record.kind == "tool_call":
        return True
    return (record.kind == "message" and record.role == "assistant"
            and not record.reasoning and record.injected is not True)


def _input_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _tool_name(record: Any) -> str:
    return record.name if isinstance(record.name, str) and record.name else "?"


def convert(records: Iterable[Any], *, source_key: str, main_id: str,
            agent_id: str | None, label: str | None, policy: Policy,
            secret_calls: set[str] | frozenset[str] = frozenset()) -> Converted:
    """The rows and tails one batch of records gives.

    `main_id` is the main session's id, which every row carries; `agent_id` is the
    subagent's own id for a subagent's records, else None. `secret_calls` holds the call
    ids already known to be secret-path calls of this source, whose results give no
    tail. A subagent's records give tier 1 rows only."""
    records = list(records)
    out = Converted()
    known_secret = set(secret_calls)
    for record, key in zip(records, keys(records, source_key)):
        when = _known(record.time)
        if isinstance(when, (int, float)):
            out.newest = when if out.newest is None else max(out.newest, when)
        common = {"key": key, "harness": record.harness, "session_id": main_id,
                  "label": label, "time": float(when) if isinstance(when, (int, float))
                  else None, "turn_id": None if _known(record.turn_id) is None
                  else str(record.turn_id)}
        if record.kind == "message":
            if not tier_one_message(record):
                continue
            text = keep_head(record.text, MESSAGE_CHARS, policy.sets)
            out.rows.append(Row(**common, agent_id=agent_id, kind=record.role, text=text))
        elif record.kind == "tool_call":
            name = _tool_name(record)
            path = secret_path(record.input)
            if path is not None:
                call_id = _known(record.call_id)
                if call_id is not None:
                    known_secret.add(str(call_id))
                    out.secret_calls.add(str(call_id))
                text = keep_head(f"{name}({path})", TOOL_CHARS, policy.sets)
            else:
                summary = record.summary or f"{name}()"
                body = _input_text(record.input)
                text = keep_head(f"{summary}\n{body}" if body else summary, TOOL_CHARS,
                                 policy.sets)
            out.rows.append(Row(**common, agent_id=agent_id, kind=TOOL, text=text))
        elif record.kind == "tool_result":
            if agent_id is not None:
                continue
            call_id = _known(record.call_id)
            if call_id is not None and str(call_id) in known_secret:
                continue
            output = record.output if isinstance(record.output, str) else ""
            if not output.strip():
                continue
            text = keep_tail(output, policy.tail_chars, policy.sets)
            out.tails.append(Tail(**common, failed=record.is_error is True, text=text))
    return out


__all__ = ["KINDS", "MESSAGE_CHARS", "TOOL_CHARS", "MARGIN", "EDGE_CHARS", "TOOL",
           "UNSCANNED", "Policy", "Row", "Tail", "Converted", "redact_version", "redact",
           "keep_head", "keep_tail", "recut_head",
           "recut_tail", "secret_path",
           "line_id", "keys", "tier_one_message", "model_answer", "convert"]
