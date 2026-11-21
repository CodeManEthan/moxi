"""Who wrote a message, and what a message opens with.

`writer(record)` names who a message record's harness says wrote it, from the record's
role, `injected`, `origin_kind` and flags, and the fields that make sessions follow a
message with a compaction marker; never from its text. One of:

* `person`: a user message the harness marks as typed at the session's prompt;
* `model`: the session's model writing its own turn, or a tool call it made;
* `peer` and `coordinator`: mail another agent's session sent in, named only by the kind
  of the line's own `origin` object (`Message.origin_kind`), never by `Message.origin`
  alone, which falls back to the line's `userType`;
* `unknown`: a user message whose submitter the harness doesn't say;
* `channel`, `hook` (a plugin's), `task` (a background task's notification), `harness`
  (a message the harness composed: a meta line whose origin names none of the kinds
  above, an injected context, a system or developer message, a turn it made up, a wrapper
  or interrupt line) and `summary` (a compaction summary the harness asked the model
  for).

An injected user message's origin kind is read before its meta flag: Claude Code marks
peer, coordinator and channel lines, and some plugin and task lines, `isMeta` beside
the origin that names their sender.

A record that is neither a message nor a tool call has no writer: None.

`OPENING` is the characters every test of what a message opens with skips first: U+0000
to U+0020, U+007F to U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F,
U+3000, and every character of Unicode category Cf. That holds every character
`str.isspace()` takes and every character a chat host's scrub removes. `opening(text)`
drops those characters from the front. The string is built from `unicodedata` when it is
first asked for; `opening` needs no such build.

`OUTPUT_WRAPPERS` is every tag a harness or a fleet tool puts around output in a user
message: Claude Code's (`claude.WRAPPER_TAGS`), Codex's (`codex.WRAPPER_TAGS`) and
`SHARED_WRAPPERS`. `opens_with_output(text)` says whether a text opens with one after
the opening characters, and `opens_with_tag(text)` whether it opens with `<` followed by
a letter of any script (`str.isalpha`).
"""

from __future__ import annotations

from typing import Any

from .shape import OPENCODE, Record

PERSON = "person"
MODEL = "model"
PEER = "peer"
COORDINATOR = "coordinator"
UNKNOWN_WRITER = "unknown"
CHANNEL = "channel"
HOOK = "hook"
TASK = "task"
HARNESS = "harness"
SUMMARY = "summary"
#: Every value `writer` gives, None aside.
WRITERS = (PERSON, MODEL, PEER, COORDINATOR, UNKNOWN_WRITER, CHANNEL, HOOK, TASK,
           HARNESS, SUMMARY)

#: The writer of an injected user message, by the kind its line's `origin` object names.
#: An injected message whose origin kind isn't here was composed by the harness.
ORIGIN_WRITERS = {"peer": PEER, "coordinator": COORDINATOR, "channel": CHANNEL,
                  "plugin": HOOK, "task-notification": TASK}

#: The tags fleet tools and the harnesses put around output in a user message, beside the
#: two harnesses' own lists. A tag without its `>` matches with attributes after it.
SHARED_WRAPPERS = ("<bash-stderr>", "<task-notification>", "<hook_prompt",
                   "<user_shell_command>", "<system-reminder>", "<local-command-caveat>")

#: The single characters the opening set holds besides the Cf ones.
_SINGLES = frozenset("     　")


def writer(record: Record) -> str | None:
    """Who the record's harness says wrote it; None for a record that is neither a
    message nor a tool call."""
    if record.kind == "tool_call":
        return MODEL
    if record.kind != "message":
        return None
    flags = record.flags if isinstance(record.flags, dict) else {}
    role = record.role
    if flags.get("isCompactSummary") is True or _opencode_summary(record, flags):
        return SUMMARY
    injected = record.injected
    kind = record.origin_kind
    if role == "user" and injected is True and isinstance(kind, str) \
            and kind in ORIGIN_WRITERS:
        return ORIGIN_WRITERS[kind]
    if flags.get("isMeta") is True:
        return HARNESS
    if role == "assistant":
        return MODEL if injected is False else HARNESS
    if role != "user":
        return HARNESS
    if injected is False:
        return PERSON
    if injected is True:
        return HARNESS
    return UNKNOWN_WRITER


def _opencode_summary(record: Record, flags: dict) -> bool:
    """An OpenCode assistant message sessions follows with a compaction marker."""
    if record.harness != OPENCODE or record.role != "assistant":
        return False
    extra = record.extra if isinstance(record.extra, dict) else {}
    return extra.get("agent") == "compaction" or flags.get("summary") is True


def skipped(c: str) -> bool:
    """Whether `c` is one of the opening characters."""
    if c <= "\x20" or "\x7f" <= c <= "\xa0" or " " <= c <= " " or c in _SINGLES:
        return True
    if c.isascii():
        return False
    import unicodedata

    return unicodedata.category(c) == "Cf"


def opening(text: str) -> str:
    """`text` without the opening characters at its front."""
    for i, c in enumerate(text):
        if not skipped(c):
            return text[i:] if i else text
    return ""


def opens_with_output(text: str) -> bool:
    """Whether `text` opens, after the opening characters, with an output wrapper."""
    return opening(text).startswith(output_wrappers())


def opens_with_tag(text: str) -> bool:
    """Whether `text` opens, after the opening characters, with `<` and a letter of any
    script (`str.isalpha`)."""
    head = opening(text)[:2]
    return len(head) == 2 and head[0] == "<" and head[1].isalpha()


def output_wrappers() -> tuple[str, ...]:
    from . import claude, codex

    return (*claude.WRAPPER_TAGS, *codex.WRAPPER_TAGS, *SHARED_WRAPPERS)


def _opening_string() -> str:
    import sys
    import unicodedata

    return "".join(chr(code) for code in range(sys.maxunicode + 1)
                   if (code <= 0x20 or 0x7F <= code <= 0xA0 or 0x2000 <= code <= 0x200A
                       or chr(code) in _SINGLES)
                   or unicodedata.category(chr(code)) == "Cf")


def __getattr__(name: str) -> Any:
    if name == "OPENING":
        value = _opening_string()
    elif name == "OUTPUT_WRAPPERS":
        value = output_wrappers()
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


__all__ = ["COORDINATOR", "HARNESS", "MODEL", "OPENING", "OUTPUT_WRAPPERS", "PEER",
           "PERSON", "SHARED_WRAPPERS", "SUMMARY", "UNKNOWN_WRITER", "WRITERS", "opening",
           "opens_with_output", "opens_with_tag", "skipped", "writer"]

