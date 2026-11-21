"""The scrub every answer gets before it leaves the door.

The connector removes, from a tool answer's text part and from every string of its
structured value at any depth (keys and values alike):

- the C0 control characters except newline and tab;
- DEL;
- the C1 range, U+0080 to U+009F;
- every character of Unicode category `Cf`: the bidi controls, the zero-width characters,
  the soft hyphen, the byte order mark and the tag characters among them.

Nothing else is touched. Tab stays because notes hold tab-separated files. These are the
characters that let text hide words from a reader's eye, or split a token so that a
pattern no longer matches it, while a model still reads every one.

It runs after the provider answers, so no provider can forget it. A provider that redacts
redacts the raw text, removes these same characters, then redacts again, so its last
redaction saw the text in the form the scrub leaves, and the scrub changes nothing
redaction didn't see (`moxi_connector.provider` states that rule).

Standard library only.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any

#: The control characters removed by table: C0 but newline and tab, DEL, and C1.
_CONTROLS = {c: None for c in range(0x20) if c not in (0x09, 0x0A)}
_CONTROLS[0x7F] = None
_CONTROLS.update({c: None for c in range(0x80, 0xA0)})
#: The Unicode category removed beyond the control ranges.
_FORMAT = "Cf"


def scrub_text(text: str) -> str:
    """`text` with the control and format characters above removed."""
    text = text.translate(_CONTROLS)
    if text.isascii():
        return text
    return "".join(ch for ch in text if unicodedata.category(ch) != _FORMAT)


def scrub_value(value: Any) -> Any:
    """`value` with every string in it scrubbed, at any depth of mappings, lists and
    tuples; a mapping comes back a dict and a tuple a list, as JSON reads them. Any other
    value comes back as it is."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {scrub_value(key): scrub_value(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(inner) for inner in value]
    return value


__all__ = ["scrub_text", "scrub_value"]
