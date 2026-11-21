"""Local paths cut to their last segment, for text that leaves the machine.

`cut_local(text, env=None)` is the one cut. Loopback origins go first: `localhost`, any
`127.x.x.x`, `127.1`, `0.0.0.0`, `[::1]`, `::1` and `0:0:0:0:0:0:0:1`, as a whole token
(not preceded by a letter, digit, `.` or `-`, not followed by a letter, digit or `-`),
with any scheme before it and any `:port` and path, query or fragment after it. Each
becomes `local`.

Local paths go second, cut by root wherever a root occurs, whatever precedes it, matched
without regard to case. The roots are the home folder `env` names (`HOME`; the process
environment when `env` is None), `~/`, `$HOME/` and `SYSTEM_ROOTS`. A path runs to the
first whitespace, quote, backtick or one of `<>|,;(){}[]`, or the text's end, and is
replaced by its last non-empty segment cut to `SEGMENT_MAX_BYTES`, or `note` when it has
none.

`cut_local` changes text, so a reader that redacts runs it inside secrets' `chat_redact`,
as its `between`, never before or after it: a cut can glue a word onto a secret and take
away the boundary the secret's rule needs, and only the redaction after it judges the
text as sent.

`last_segment(path)` is a folder's own cut, which takes any path, not only one under a
root. It splits a path on `/` and drops the empty parts, so a trailing slash or a
relative path cuts the same way, and gives the last part, cut to `SEGMENT_MAX_BYTES`
UTF-8 bytes on a character boundary. A path with no segment (`/`) gives `/`; an empty or
missing path gives None.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Mapping

#: What a loopback origin becomes.
LOOPBACK = "local"
#: What a path in a text with no segment becomes.
NO_SEGMENT = "note"
#: What a folder with no segment becomes.
ROOT_SEGMENT = "/"
#: The most of a path's last segment that goes out, in UTF-8 bytes.
SEGMENT_MAX_BYTES = 255
#: The roots every path under which is cut, besides the home folder, `~/` and `$HOME/`.
SYSTEM_ROOTS = ("/home/", "/tmp/", "/root/", "/etc/", "/var/", "/usr/", "/opt/", "/mnt/",
                "/media/", "/run/", "/srv/")

_RUN = r"""[^\s"'`<>|,;(){}\[\]]*"""
_LOOPBACK = re.compile(
    r"(?<![A-Za-z0-9.\-])"
    r"(?:[A-Za-z][A-Za-z0-9+.\-]*://)?"
    r"(?:localhost|127\.\d{1,3}+\.\d{1,3}+\.\d{1,3}+|127\.1|0\.0\.0\.0"
    r"|\[(?:::1|0:0:0:0:0:0:0:1)\]|::1|0:0:0:0:0:0:0:1)"
    r"(?![A-Za-z0-9\-])"
    r"(?::\d++)?"
    r"(?:[/?#]" + _RUN + r")?",
    re.IGNORECASE)


def last_segment(path: str | None) -> str | None:
    """The last part of `path`, cut to `SEGMENT_MAX_BYTES` bytes; `/` for a path with no
    part; None for an empty or missing path."""
    if not isinstance(path, str) or not path:
        return None
    parts = [part for part in path.split("/") if part]
    if not parts:
        return ROOT_SEGMENT
    return _bounded(parts[-1]) or ROOT_SEGMENT


def _bounded(part: str) -> str:
    data = part.encode("utf-8", "surrogatepass")
    if len(data) <= SEGMENT_MAX_BYTES:
        return part
    return data[:SEGMENT_MAX_BYTES].decode("utf-8", "ignore")


def _roots(home: str | None) -> tuple[str, ...]:
    roots = {"~/", "$HOME/", *SYSTEM_ROOTS}
    home = (home or "").rstrip("/")
    if home.startswith("/"):
        roots.add(home + "/")
    return tuple(sorted(roots, key=len, reverse=True))


@functools.lru_cache(maxsize=8)
def _paths(home: str | None) -> re.Pattern:
    return re.compile("(?:" + "|".join(re.escape(root) for root in _roots(home)) + ")"
                      + _RUN, re.IGNORECASE)


def _display(match: re.Match) -> str:
    parts = [part for part in match.group(0).split("/") if part]
    return (_bounded(parts[-1]) if parts else "") or NO_SEGMENT


def cut_local(text: str, env: Mapping[str, str] | None = None) -> str:
    """`text` with each loopback origin made `local`, then each local path under a root
    cut to its last segment. `env` names the home folder (`HOME`); None reads the
    process environment."""
    home = (os.environ if env is None else env).get("HOME")
    return _paths(home).sub(_display, _LOOPBACK.sub(LOOPBACK, text))


__all__ = ["LOOPBACK", "NO_SEGMENT", "ROOT_SEGMENT", "SEGMENT_MAX_BYTES", "SYSTEM_ROOTS",
           "cut_local", "last_segment"]
