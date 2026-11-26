"""One session's turns as sessions shows them: the view row of each record, which sessions'
page sends, and the lines `moxi sessions read` prints from the same rows.

**The view row** (`view_row`) is a record as a row whose keys are those its kind carries,
never the record's JSON: no `extra`, no `raw` and no tool call's input. A tool call is its
one-line summary (`ToolCall.summary`), a tool result its size and whether it failed, a
message its text cut at `TEXT_MAX` UTF-8 bytes with `truncated` set. A value the harness
did not say is left out of the row and named in its `unknown` list, and so is a value of a
type the row does not carry.

**Tool output.** Only given `outputs` (an `Outputs`, which `moxi sessions read --results`
passes) does a tool result's row carry its output: `output`, the text redacted and then
cut to its first and last `OUTPUT_KEEP_CHARS` characters (put end to end),
`output_cut_chars`, how many characters were cut from between them (0 for a whole output),
and `withheld_why`, null, or why the output is not shown: its call's input names a secret
path (`moxi_secrets.secret_path`), or its call is not among the records read, so the read
can't tell. A withheld output's `output` is null. Without `outputs` no row carries output,
which is how sessions' page and every other read have it.

**Redaction.** Given `redact`, a function from text to text, `view_row` passes each
message's whole text, each tool call's name and whole argument, and each title through it
before any cut, so a cut never leaves part of a secret that no longer matches its pattern.
A tool call's summary is then made again from its input (`shape.tool_summary`): put on
one line, then redacted, then cut at 160 characters (`shape.squeezed_first`), and the
row's `name` and `server` are redacted too. Without
`redact` the row is the record's own.

`redactor()` is the redaction `moxi sessions read` uses: secrets' `credentials` and `broad`
sets (`SETS`), fixed, through the secrets module when it loads, reached through
`moxi.modules.load` because secrets is an optional link of sessions. When it doesn't load,
`redactor()` says why and the read prints its text as written.

**The reads.** `turns_last` and `turns_around` give turns of records, which a reader
that needs more than the rows (who wrote each message) works from; `last` and `around`
make rows of them. `last` is sessions' `window`: the last turns. `around` reads the records
forward and keeps the turns around a time: the turn that holds it (the newest turn that
started at or before it, else the first), up to half the rest before it and the others
after, with more before when the session ends first. `whole` is every record. Each gives a
`Shown`, whose rows come from the source's one snapshot. A turn is a run of records of one
turn id, as `window` counts it; records whose turn the harness did not say join the run
they fall in.

`lines(row)` is the text a row prints as, the way sessions' page draws it: a message as
its label and its text, a tool call as its summary line, a tool result as `Result:` with
its size (and, when the row carries it, its output below, or why it is withheld), a marker
as a labelled rule, a request as its model and token counts.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from .shape import CODEX, UNKNOWN, Record, squeezed_first, tool_summary

#: The most of one message's text a row carries, in UTF-8 bytes.
TEXT_MAX = 65536
#: The sets `moxi sessions read` redacts with. They are sessions' own and fixed.
SETS = ("credentials", "broad")
#: The secrets interface the read asks for.
SECRETS_INTERFACE = 1

#: A function from text to its redacted text.
Redact = Callable[[str], str]
#: secrets' test of a tool call's input: the secret path it names, or None.
SecretPath = Callable[[Any], "str | None"]

#: A long tool output keeps this many characters at each end.
OUTPUT_KEEP_CHARS = 4000
#: Why an output is withheld: its call's input names a secret path.
WITHHELD_SECRET = "output withheld: the call read a secret path"
#: Why an output is withheld: its call is not among the records read.
WITHHELD_UNSEEN = "output withheld: its call is not in this read, so it may read a secret path"

STRING = "string"
INT = "int"
FLOAT = "float"
BOOL = "bool"

#: Each kind's fields beyond the common ones, with the type the row carries each as.
FIELDS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "message": (("role", STRING), ("reasoning", BOOL), ("model", STRING),
                ("synthetic", BOOL)),
    "tool_call": (("call_id", STRING), ("name", STRING), ("summary", STRING),
                  ("server", STRING)),
    "tool_result": (("call_id", STRING), ("is_error", BOOL), ("size_bytes", INT)),
    "request": (("request_id", STRING), ("model", STRING), ("response_model", STRING),
                ("provider", STRING), ("service_tier", STRING), ("input_tokens", INT),
                ("cache_read_input_tokens", INT), ("cache_creation_input_tokens", INT),
                ("output_tokens", INT), ("reasoning_output_tokens", INT),
                ("cost_usd", FLOAT), ("context_window", INT), ("sidechain", BOOL),
                ("synthetic", BOOL), ("final", BOOL)),
    "limits": (("limit_id", STRING), ("limit_name", STRING), ("plan_type", STRING)),
    "malformed": (("why", STRING), ("unterminated", BOOL)),
    "other": (("recognised", BOOL),),
}

#: A marker's own fields the row carries, by the marker's name: (field, row key, type).
MARKER_FIELDS: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "compaction": (("trigger", "trigger", STRING), ("pre_tokens", "pre_tokens", INT)),
    "turn_end": (("duration_ms", "duration_ms", INT),),
    "title": (("title", "title", STRING), ("source", "title_source", STRING)),
    "settings": (("model", "model", STRING), ("provider", "provider", STRING),
                 ("service_tier", "service_tier", STRING)),
}

#: The row keys a None cannot fill: those the row declares not nullable.
NOT_NULL = frozenset({"reasoning", "size_bytes", "request_id", "final", "why",
                      "unterminated", "recognised", "summary"})


def fits(value: Any, kind: str) -> bool:
    if kind == STRING:
        return isinstance(value, str)
    if kind == BOOL:
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind == INT:
        return isinstance(value, int)
    return isinstance(value, (int, float)) and value == value and value not in (
        float("inf"), float("-inf"))


def put(row: dict[str, Any], unknown: list[str], key: str, value: Any, kind: str) -> None:
    """Set `row[key]`, or name `key` in `unknown` when the harness did not say or wrote a
    value the row cannot carry."""
    if value is None and key not in NOT_NULL:
        row[key] = None
    elif value is not UNKNOWN and value is not None and fits(value, kind):
        row[key] = value
    else:
        unknown.append(key)


def _at(at: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for key, kind in (("line", INT), ("offset", INT), ("table", STRING), ("id", STRING)):
        value = at.get(key)
        if value is None and key == "line" and key in at:
            out[key] = None
        elif value is not None and fits(value, kind):
            out[key] = value
    return out


def cut_text(text: str) -> tuple[str, int, bool]:
    """The text cut to `TEXT_MAX` UTF-8 bytes on a character's edge, its whole size, and
    whether it was cut."""
    data = text.encode("utf-8", "surrogatepass")
    if len(data) <= TEXT_MAX:
        return text, len(data), False
    return data[:TEXT_MAX].decode("utf-8", "ignore"), len(data), True


def cut_message(said: dict[str, Any] | None, redact: Redact | None = None
                ) -> dict[str, Any] | None:
    """A list's prompt or reply as it is sent or printed: `{at, text, truncated}`, the
    text redacted when `redact` is given and then cut at `TEXT_MAX` bytes. `truncated` is
    true when the text was cut here or came cut already (a prompt, which the catalog
    keeps cut). None stays None."""
    if said is None:
        return None
    text, _, truncated = cut_text(redact(said["text"]) if redact else said["text"])
    return {"at": said["at"], "text": text,
            "truncated": truncated or said.get("truncated") is True}


def _windows(windows: Any) -> list[dict[str, Any]]:
    out = []
    for item in windows or ():
        row: dict[str, Any] = {"name": str(getattr(item, "name", ""))}
        unknown: list[str] = []
        put(row, unknown, "used_percent", getattr(item, "used_percent", UNKNOWN), FLOAT)
        put(row, unknown, "window_minutes", getattr(item, "window_minutes", UNKNOWN), INT)
        put(row, unknown, "resets_at", getattr(item, "resets_at", UNKNOWN), FLOAT)
        row["unknown"] = unknown
        out.append(row)
    return out


def summary(record: Record, redact: Redact | None = None) -> str:
    """A tool call's summary line: the record's own without `redact`; with it, made again
    from the call's input, `redact` making the name's and the whole argument's line
    before the cut (`shape.tool_summary` says what it must do)."""
    if redact is None:
        return record.summary
    tool_input = record.input
    if record.harness == CODEX:
        from .codex import summary_input

        tool_input = summary_input(tool_input)
    return tool_summary(record.name, tool_input, redact=redact)


def _redacted(value: Any, redact: Redact | None) -> Any:
    return redact(value) if redact is not None and isinstance(value, str) else value


def cut_output(text: str) -> tuple[str, int]:
    """A tool output cut to its first and last `OUTPUT_KEEP_CHARS` characters, put end to
    end, and how many characters were cut from between them."""
    if len(text) <= 2 * OUTPUT_KEEP_CHARS:
        return text, 0
    return (text[:OUTPUT_KEEP_CHARS] + text[-OUTPUT_KEEP_CHARS:],
            len(text) - 2 * OUTPUT_KEEP_CHARS)


@dataclass
class Outputs:
    """The tool output a read shows, for rows made in the session's order. `secret_path`
    is secrets' test of a call's input; `calls` holds, by call id, whether each tool call
    seen so far names a secret path."""

    secret_path: SecretPath
    calls: dict[str, bool] = field(default_factory=dict)

    def note(self, record: Record) -> None:
        """Remember a tool call's id and whether its input names a secret path."""
        call_id = record.call_id
        if isinstance(call_id, str) and call_id:
            secret = self.secret_path(record.input) is not None
            self.calls[call_id] = self.calls.get(call_id, False) or secret

    def put(self, row: dict[str, Any], record: Record, redact: Redact | None) -> None:
        """Set a tool result row's `output`, `output_cut_chars` and `withheld_why`."""
        call_id = record.call_id
        secret = self.calls.get(call_id) if isinstance(call_id, str) else None
        if secret is None or secret:
            row["output"], row["output_cut_chars"] = None, 0
            row["withheld_why"] = WITHHELD_UNSEEN if secret is None else WITHHELD_SECRET
            return
        text = record.output if isinstance(record.output, str) else ""
        row["output"], row["output_cut_chars"] = cut_output(_redacted(text, redact))
        row["withheld_why"] = None


def view_row(record: Record, *, redact: Redact | None = None,
             outputs: Outputs | None = None) -> dict[str, Any]:
    """One record as its view row, with its text redacted first when `redact` is given.
    With `outputs`, a tool call is noted there and a tool result's row carries its
    output, as the module docstring says; rows must then be made in the session's
    order."""
    kind = record.kind
    row: dict[str, Any] = {"kind": kind, "raw_kind": record.raw_kind or "",
                           "at": _at(record.at)}
    unknown: list[str] = []
    put(row, unknown, "time", record.time, FLOAT)
    put(row, unknown, "turn_id", record.turn_id, STRING)
    put(row, unknown, "agent_id", record.agent_id, STRING)
    for name, typed in FIELDS.get(kind, ()):
        value = getattr(record, name)
        if kind == "tool_call" and name == "summary":
            value = summary(record, squeezed_first(redact) if redact else None)
        elif kind == "tool_call" and name in ("name", "server"):
            value = _redacted(value, redact)
        put(row, unknown, name, value, typed)
    if kind == "message":
        text = record.text if isinstance(record.text, str) else ""
        row["text"], row["text_bytes"], row["truncated"] = cut_text(_redacted(text, redact))
        row["flags"] = sorted(name for name, value in (record.flags or {}).items()
                              if value is True)
    elif kind == "marker":
        row["marker"] = record.marker
        for field, key, typed in MARKER_FIELDS.get(record.marker, ()):
            value = record.fields.get(field, UNKNOWN)
            if field == "title":
                value = _redacted(value, redact)
            put(row, unknown, key, value, typed)
    elif kind == "limits":
        row["windows"] = _windows(record.windows)
    elif outputs is not None and kind == "tool_call":
        outputs.note(record)
    elif outputs is not None and kind == "tool_result":
        outputs.put(row, record, redact)
    row["unknown"] = unknown
    return row


def header_row(found: Any, rows: list[dict[str, Any]], *,
               redact: Redact | None = None) -> dict[str, Any]:
    """The session facts beside the rows: `cwd`, `title`, `version`, `parent` and
    `entrypoint`, each left out and named in `unknown` when the harness did not say it.
    `title` is the header's, else the newest title among the rows."""
    header: dict[str, Any] = {}
    unknown: list[str] = []
    for name in ("cwd", "title", "version", "parent", "entrypoint"):
        value = getattr(found, name, UNKNOWN)
        if name == "title":
            value = _redacted(value, redact)
        put(header, unknown, name, value, STRING)
    if "title" in unknown or header.get("title") is None:
        titles = [r["title"] for r in rows if r.get("marker") == "title"
                  and isinstance(r.get("title"), str)]
        if titles:
            header["title"] = titles[-1]
            if "title" in unknown:
                unknown.remove("title")
    header["unknown"] = unknown
    return header


# --- redaction ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Redactor:
    """The read's redaction: `redact`, or None with `reason`, why the text goes as
    written. `secret_path` is secrets' test of a tool call's input, None when secrets
    offers none (or there is no redaction)."""

    redact: Redact | None
    reason: str | None = None
    secret_path: SecretPath | None = None


def redactor() -> Redactor:
    """secrets' `redact` over `SETS` and its `secret_path`, or why there is no redaction:
    secrets is not installed, does not load, or cannot load the sets."""
    from moxi import modules

    found = modules.load("secrets", interface=SECRETS_INTERFACE)
    if not found.ok:
        return Redactor(None, f"the secrets module is {found.state}"
                              + (f" ({found.reason})" if found.reason else ""))
    scan_redact = getattr(found.api, "redact", None)
    if not callable(scan_redact):
        return Redactor(None, "the secrets module offers no redact")
    try:
        scan_redact("", SETS)
    except Exception as exc:  # noqa: BLE001 - any failure means no redaction to offer
        return Redactor(None, f"secrets cannot load the sets {', '.join(SETS)}: {exc}")

    def redact(text: str) -> str:
        return scan_redact(text, SETS)

    secret_path = getattr(found.api, "secret_path", None)
    return Redactor(redact, secret_path=secret_path if callable(secret_path) else None)


# --- the reads ------------------------------------------------------------------------------

@dataclass
class Shown:
    """What a read shows: the session's `header` (a `Header`, read when first asked for
    through `header_of`), its `rows` in order, and where they sit in the session.

    * `mode`: `last`, `around` or `all`.
    * `turns`: the turns asked for (None for `all`).
    * `earlier`: the session has records before these; `later`: and after them (always
      false for `last` and `all`).
    * `ended`: for `last`, how the backward read ended (`start`, `limit` or None).
    """

    mode: str
    rows: Iterator[dict[str, Any]] | list[dict[str, Any]]
    header_of: Callable[[], Any]
    turns: int | None = None
    around: float | None = None
    earlier: bool = False
    later: bool = False
    ended: str | None = None


def last(source: Any, turns: int, redact: Redact | None = None, *,
         outputs: Outputs | None = None) -> Shown:
    """The last `turns` turns, through sessions' `window`."""
    from .read import window

    read = window(source, turns=turns)
    rows = [view_row(record, redact=redact, outputs=outputs) for record in read.records]
    return Shown("last", rows, lambda: read.header, turns=turns,
                 earlier=read.has_earlier, ended=read.ended)


def _turn(record: Record) -> Any:
    turn = record.turn_id
    return None if turn is UNKNOWN else turn


@dataclass
class _Group:
    records: list[Record]
    turn: Any = None
    start: float | None = None


def _groups(records: Iterator[Record]) -> Iterator[_Group]:
    """The records as turns, oldest first. A record of a turn already passed (a Claude
    request comes where it ended, after the next turn's first line) joins the run it falls
    in, as `window` counts it. A group's `start` is the first time its records say, the
    time its rows would carry."""
    group = _Group([])
    seen: set[Any] = set()
    for record in records:
        turn = _turn(record)
        if turn is not None and turn not in seen:
            seen.add(turn)
            if group.turn is None:
                group.turn = turn
            else:
                yield group
                group = _Group([], turn)
        if group.start is None and fits(record.time, FLOAT):
            group.start = record.time
        group.records.append(record)
    if group.records:
        yield group


@dataclass
class Turns:
    """Turns of records, oldest first, each a list of records: what `turns_last` and
    `turns_around` read. `header_of`, `earlier`, `later` and `ended` are as `Shown`
    says."""

    groups: list[list[Record]]
    header_of: Callable[[], Any]
    earlier: bool = False
    later: bool = False
    ended: str | None = None


def turns_last(source: Any, turns: int) -> Turns:
    """The last `turns` turns as records, through sessions' `window`."""
    from .read import window

    read = window(source, turns=turns)
    groups = [group.records for group in _groups(iter(read.records))]
    return Turns(groups, lambda: read.header, earlier=read.has_earlier, ended=read.ended)


def turns_around(source: Any, at: float, turns: int) -> Turns:
    """`turns` turns around the time `at` (epoch seconds) as records, read forward."""
    from .read import records

    before = (turns - 1) // 2
    after_max = turns - 1
    kept: deque[_Group] = deque(maxlen=turns)
    after: list[_Group] = []
    dropped = later = placed = False
    with records(source) as read:
        header = read.header
        for group in _groups(iter(read)):
            if not placed:
                if kept and group.start is not None and group.start > at:
                    placed = True
                    # Fewer turns before the one that holds the time leave more after it.
                    after_max = turns - 1 - min(before, len(kept) - 1)
                else:
                    dropped = dropped or len(kept) == kept.maxlen
                    kept.append(group)
                    continue
            if len(after) == after_max:
                later = True
                break
            after.append(group)
    take = min(len(kept) - 1, turns - 1 - len(after))
    first = len(kept) - 1 - take
    chosen = list(kept)[first:] + after
    return Turns([group.records for group in chosen], lambda: header,
                 earlier=dropped or first > 0, later=later)


def around(source: Any, at: float, turns: int, redact: Redact | None = None, *,
           outputs: Outputs | None = None) -> Shown:
    """`turns` turns around the time `at` (epoch seconds), read forward. Only the turns
    chosen are made rows, so the turns passed over on the way cost no redaction."""
    read = turns_around(source, at, turns)
    rows = [view_row(record, redact=redact, outputs=outputs)
            for group in read.groups for record in group]
    return Shown("around", rows, read.header_of, turns=turns, around=at,
                 earlier=read.earlier, later=read.later)


def whole(source: Any, redact: Redact | None = None, *,
          outputs: Outputs | None = None) -> Shown:
    """Every record of the session, as rows read one at a time. The source stays open
    until the rows run out."""
    from .read import records

    read = records(source)

    def rows() -> Iterator[dict[str, Any]]:
        with read:
            for record in read:
                yield view_row(record, redact=redact, outputs=outputs)

    return Shown("all", rows(), lambda: read.header)


# --- the text -------------------------------------------------------------------------------

HARNESS_LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
ROLE_LABELS = {"user": "User", "assistant": "Assistant", "system": "System",
               "developer": "Developer"}
#: A synthetic message's first line is shown up to this many characters.
SYNTHETIC_CUT = 200
#: What a row with no time shows in the time's place.
NO_TIME = "(no time)".ljust(20)


def stamp(epoch: float) -> str:
    """Epoch seconds as UTC `YYYY-MM-DDTHH:MM:SSZ`, the form `--around` takes back."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _count(n: int | float) -> str:
    return f"{round(n):,}"


def _bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms} ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f} s"
    s = round(ms / 1000)
    return f"{s // 60} min {s % 60} s"


def request_line(row: Mapping[str, Any]) -> str:
    parts = [row.get("model") or row.get("response_model") or "an unnamed model"]
    for key, label in (("input_tokens", "in"), ("cache_read_input_tokens", "cache read"),
                       ("cache_creation_input_tokens", "cache write"),
                       ("output_tokens", "out"), ("reasoning_output_tokens", "reasoning")):
        if isinstance(row.get(key), int):
            parts.append(f"{_count(row[key])} {label}")
    if isinstance(row.get("cost_usd"), (int, float)):
        parts.append(f"${row['cost_usd']:.4f}")
    if row.get("synthetic") is True:
        parts.append("synthetic")
    return " · ".join(parts)


def marker_line(row: Mapping[str, Any]) -> str:
    marker = row.get("marker")
    if marker == "compaction":
        how = [part for part in (row.get("trigger"),
                                 f"{_count(row['pre_tokens'])} tokens before"
                                 if isinstance(row.get("pre_tokens"), int) else None)
               if isinstance(part, str)]
        return f"Compacted ({', '.join(how)})" if how else "Compacted"
    if marker == "turn_start":
        return "Turn started"
    if marker == "turn_end":
        ms = row.get("duration_ms")
        return f"Turn ended after {_duration(ms)}" if isinstance(ms, int) else "Turn ended"
    if marker == "turn_aborted":
        return "Turn aborted"
    if marker == "rollback":
        return "Rolled back"
    if marker == "settings":
        what = [f"{label} {row[key]}" for key, label in (
            ("model", "model"), ("provider", "provider"), ("service_tier", "tier"))
            if row.get(key)]
        return f"Settings: {', '.join(what)}" if what else "Settings"
    if marker == "title":
        title = row.get("title") if isinstance(row.get("title"), str) else "(not said)"
        source = row.get("title_source")
        return f"Title ({source}): {title}" if source else f"Title: {title}"
    if marker == "session_meta":
        return "Session started"
    return f"Marker {marker or row.get('raw_kind')}"


def _limits_line(row: Mapping[str, Any]) -> str:
    name = row.get("limit_name") or row.get("limit_id") or "limits"
    windows = [f"{w['name']} {w['used_percent']}%"
               if isinstance(w.get("used_percent"), (int, float)) else f"{w['name']} unknown"
               for w in row.get("windows") or ()]
    return f"Limits ({name}): {', '.join(windows)}" if windows else f"Limits ({name})"


def _first_line_cut(text: str, n: int) -> str:
    first = text.split("\n")[0]
    return first if len(first) <= n else first[:n]


def _output_lines(row: Mapping[str, Any]) -> list[str]:
    """A tool result's output below its `Result:` line, each line indented two spaces,
    with the cut named where it falls; nothing when the row carries no output."""
    if "output" not in row:
        return []
    if row.get("withheld_why"):
        return [f"  {row['withheld_why']}"]
    text = row.get("output") or ""
    if not text:
        return ["  (no output)"]
    cut = row.get("output_cut_chars") or 0
    if not cut:
        return [f"  {line}" for line in text.split("\n")]
    head, tail = text[:OUTPUT_KEEP_CHARS], text[OUTPUT_KEEP_CHARS:]
    return ([f"  {line}" for line in head.split("\n")]
            + [f"  [cut: {_count(cut)} characters between the first and last "
               f"{_count(OUTPUT_KEEP_CHARS)}]"]
            + [f"  {line}" for line in tail.split("\n")])


def lines(row: Mapping[str, Any]) -> list[str]:
    """The lines one row prints as: its time, then what it is, and for a message its text
    below, each line indented two spaces."""
    when = stamp(row["time"]) if isinstance(row.get("time"), (int, float)) else NO_TIME
    kind = row.get("kind")
    if kind == "message":
        text = row.get("text") or ""
        if row.get("synthetic") is True:
            return [f"{when}  -- Synthetic: {_first_line_cut(text, SYNTHETIC_CUT)} --"]
        role = row.get("role")
        label = ("Thinking" if row.get("reasoning") is True
                 else ROLE_LABELS.get(role, role if isinstance(role, str) and role
                                      else "Message"))
        tags = list(row.get("flags") or ())
        if isinstance(row.get("model"), str):
            tags.append(row["model"])
        out = [f"{when}  {label}" + (f" ({', '.join(tags)})" if tags else "")]
        out += [f"  {line}" for line in text.split("\n")] if text else ["  (no text)"]
        if row.get("truncated"):
            out.append(f"  [cut: the message holds {_count(row.get('text_bytes', 0))} "
                       f"bytes; the first {TEXT_MAX // 1024} KiB are shown]")
        return out
    if kind == "tool_call":
        return [f"{when}  {row.get('summary', '')}"]
    if kind == "tool_result":
        size = (_bytes(row["size_bytes"]) if isinstance(row.get("size_bytes"), int)
                else "size unknown")
        return [f"{when}  Result: error, {size}" if row.get("is_error") is True
                else f"{when}  Result: {size}"] + _output_lines(row)
    if kind == "marker":
        return [f"{when}  -- {marker_line(row)} --"]
    if kind == "request":
        return [f"{when}  {request_line(row)}"]
    if kind == "limits":
        return [f"{when}  {_limits_line(row)}"]
    if kind == "malformed":
        how = row.get("why") or "malformed"
        if row.get("unterminated"):
            how += ", unterminated"
        return [f"{when}  A line that can't be read ({how})"]
    said = "Unrecognised record" if row.get("recognised") is False else "Record"
    return [f"{when}  {said} {row.get('raw_kind', '')}"]


__all__ = ["TEXT_MAX", "SETS", "OUTPUT_KEEP_CHARS", "WITHHELD_SECRET", "WITHHELD_UNSEEN",
           "Outputs", "Redact", "Redactor", "SecretPath", "Shown", "Turns", "around",
           "cut_message", "cut_output",
           "cut_text",
           "header_row", "last", "lines", "redactor", "stamp", "summary", "turns_around",
           "turns_last", "view_row", "whole"]
