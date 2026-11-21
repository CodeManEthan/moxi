"""The chat redaction: one way to redact a text before it goes to a reader outside the
machine, such as a chat in the Claude apps.

    from moxi_secrets import chat_redact

    safe = chat_redact(text)

`chat_redact` redacts the text with `CHAT_SETS` in strict mode, with no path, `allow` or
`known`; removes the characters a host's scrub removes (C0 controls other than newline
and tab, DEL, the C1 range and every format character, category Cf); and redacts what is
left again the same way. It returns that last text, which the last redaction saw whole,
or the unscanned placeholder alone when either pass could not scan the text. Redacting
before the removal keeps the boundary such a character gives between a word and the token
after it; redacting after it judges the text exactly as it is sent.

`between`, when given, is a reader's own change to the text (a read tool cutting local
paths, say). It runs on the text after the removal and before the second redaction, so the
last redaction sees exactly the text sent: a change that glues a word onto a secret can't
break the boundary the secret's rule needs. A reader changes a text nowhere else, apart
from cutting it to a length afterwards.

It fails closed: when a chat set can't run (python-stdnum missing for personal-data) it
raises `SetUnavailable` and returns nothing.

Standard library only at import; `unicodedata` is imported for a text that holds
anything outside ASCII.
"""

from __future__ import annotations

import re

from .scan import UNSCANNED, _set_names, placeholder, redacted

TYPE_CHECKING = False  # typing itself costs more at import than the hint is worth

if TYPE_CHECKING:
    from collections.abc import Callable

#: The sets every chat read redacts with, fixed in code.
CHAT_SETS = ("credentials", "broad", "personal-data")

_ASCII_SCRUB = re.compile("[\x00-\x08\x0b-\x1f\x7f]")


def scrubbed(c: str) -> bool:
    """Whether the chat redaction removes the character `c`: a C0 control other than
    newline and tab, DEL, a C1 control, or a format character (category Cf)."""
    code = ord(c)
    if code < 0x20:
        return c not in "\n\t"
    if code < 0x7F:
        return False
    if code <= 0x9F:
        return True
    import unicodedata

    return unicodedata.category(c) == "Cf"


def scrub(text: str) -> str:
    """`text` without the characters `scrubbed` names."""
    if text.isascii():
        return _ASCII_SCRUB.sub("", text)
    return "".join(c for c in text if not scrubbed(c))


def chat_redact(text: str, *, between: Callable[[str], str] | None = None) -> str:
    """`text` redacted for a chat: the chat sets in strict mode, the scrub's characters
    removed, `between` (when given) run on what is left, then the chat sets in strict mode
    again. The unscanned placeholder alone when either pass could not scan the text.
    Raises `SetUnavailable` when a chat set can't run."""
    if not isinstance(text, str):
        raise TypeError("text is a str")
    names = _set_names(CHAT_SETS)
    first, unscanned = redacted(text, names, strict=True)
    if unscanned:
        return placeholder(UNSCANNED)
    middle = scrub(first)
    if between is not None:
        middle = between(middle)
    last, unscanned = redacted(middle, names, strict=True)
    if unscanned:
        return placeholder(UNSCANNED)
    return last
