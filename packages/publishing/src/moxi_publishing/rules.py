"""The provenance rules: shapes that say how someone works rather than who they are.

A private name is secrets' (its `private-names` set, filled by the user). These rules hold
the other kind of private text: pointers into notes, sessions and messages a reader of the
public repo can't follow. Each rule applies to commit messages, to files, or to both:

| rule | applies to | shape |
|---|---|---|
| `session-link` | messages, files | a Claude Code session URL |
| `session-pointer` | messages, files | 8 or more hex digits, `@`, an ISO date |
| `message-id` | messages | `comms:` and digits, `msg` and digits, or 12 or more digits |
| `quoted-ruling` | messages | a ruling tag on a line that also holds a quoted passage |
| `ruling-tag` | files | a design or review tag |
| `round-pointer` | files | `round <home>/<slug>`, or a memory key |

Every rule is on by default; publishing's config key `provenance.rules` lists those that
run. Some shapes below are joined from parts, so this file never spells one whole and
passes the scans it feeds.

Standard library only at import; each pattern compiles on first use.
"""

from __future__ import annotations

import re
from functools import cache

MESSAGES = "messages"
FILES = "files"

#: A design or review tag: a short capital id with a number in square brackets, or one of
#: a few capital letters with a number in parentheses, the word RULE before an id, a
#: section sign with a number, a design version, the verification and re-check forms, a
#: severity with a number, a finding with a number before a parenthesis, a probe trial, a
#: ruling attributed to the operator or to a date, a status-marked id, and a session id
#: with its hash. Case-sensitive but for one scoped group. A version string, a lint code
#: or a date with no ruling word does not match.
_RULING_TAG = (
    r"\[(?:[A-Z]{1,2}|rt|bv)[0-9]{1,3}[a-z]?\]|\([RSCFQNBIX][0-9]+\)|\bRULE [A-Z]{0,2}[0-9]+"
    r"|\u00a7 ?[0-9]|\bdesign v[0-9]|build[- ]verif|re-check (?:v|b)[0-9]"
    r"|\bB[0-9]+ verification"
    r"|\b(?:MINOR|MAJOR|BLOCKER) [0-9]|Finding [0-9]+ \(|probe trials? [0-9]"
    r"|operator" r" ruling|operator,? 20[0-9]{2}-"
    r"|(?i:\b(?:ruled|rulings?|word)\b\W+(?:[a-z]+\W+){0,2}20[0-9]{2}-[0-9]{2})"
    r"|(?:\u27d0|\u2691)\**\[?[A-Za-z]{1,3}-?[0-9]"
    r"|\[[0-9]+\|[0-9a-f]{6}\]"
)

#: A quoted passage: straight or curly double quotes around at least one word character.
_QUOTED = "\"[^\"]*\\w[^\"]*\"|\u201c[^\u201d]*\\w[^\u201d]*\u201d"

#: name -> (where it applies, pattern source, flags).
_SHAPES: dict[str, tuple[frozenset[str], str, int]] = {
    "session-link": (frozenset({MESSAGES, FILES}),
                     r"claude\.ai/code/" r"session", re.IGNORECASE),
    "session-pointer": (frozenset({MESSAGES, FILES}),
                        r"[0-9a-f]{8,}@\d{4}-\d{2}-\d{2}", re.IGNORECASE),
    "message-id": (frozenset({MESSAGES}),
                   r"comms:\s*\d+|\bmsg\s*[#:]?\s*\d+"
                   r"|(?<![0-9A-Za-z])\d{12,}(?![0-9A-Za-z])", re.IGNORECASE),
    # Matched as a pair below: the tag and a quoted passage on one line.
    "quoted-ruling": (frozenset({MESSAGES}), _RULING_TAG, 0),
    "ruling-tag": (frozenset({FILES}), _RULING_TAG, 0),
    "round-pointer": (frozenset({FILES}),
                      r"(?i:\bround [a-z-]+/[a-z0-9-]+)|\bmem [a-z0-9]+-[a-z0-9-]+", 0),
}

#: Every rule's name, in the order a refusal names them.
NAMES: tuple[str, ...] = tuple(_SHAPES)


def applies(name: str, where: str) -> bool:
    """Whether rule `name` runs on `where` (`MESSAGES` or `FILES`)."""
    return where in _SHAPES[name][0]


@cache
def _compiled(name: str) -> re.Pattern[str]:
    _, source, flags = _SHAPES[name]
    return re.compile(source, flags)


@cache
def _quoted() -> re.Pattern[str]:
    return re.compile(_QUOTED)


def hits(line: str, where: str, names=NAMES) -> list[tuple[str, str]]:
    """Each rule among `names` that applies to `where` and that `line` breaks, as
    (rule, the text it matched), in `NAMES` order; empty when none."""
    found = []
    for name in NAMES:
        if name not in names or not applies(name, where):
            continue
        match = _compiled(name).search(line)
        if match is None:
            continue
        if name == "quoted-ruling":
            if _quoted().search(line) is None:
                continue
        found.append((name, match.group(0)))
    return found


def enabled_rules() -> tuple[str, ...]:
    """The rules publishing's config lets run. Raises `moxi.config.ConfigError` when the
    config can't be read."""
    from moxi_publishing import settings

    return tuple(settings.load()[settings.RULES])


__all__ = ["FILES", "MESSAGES", "NAMES", "applies", "enabled_rules", "hits"]
