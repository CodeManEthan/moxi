"""Moxi's secrets: named pattern sets and one way to look for them in text.

    from moxi_secrets import scan, redact

    hits = scan(text, ("credentials", "private-names"), path="docs/setup.md")
    clean = redact(text, ("credentials",))

The sets are "credentials" (gitleaks' pinned rules and a local list), "broad"
(catch-alls), "private-names" (the user's own list in secrets' config) and
"personal-data" (payment cards, IBANs, US SSNs, behind the `personal-data` extra). A
caller always names the sets it wants; secrets returns hits and redacted text and leaves
the action to the caller. `chat_redact(text)` is the one redaction for a text bound
for a reader outside the machine.

Standard library only at import.
"""

from .allow import Allowlist, AllowlistError
from .chat import CHAT_SETS, chat_redact
from .names import PrivateNames, private_names
from .paths import is_secret_path, secret_path
from .scan import (REDACT_VERSION, SETS, Hit, LineHit, SetInfo, SetUnavailable, describe,
                   redact, scan, scan_lines, seen_through)

__all__ = ["CHAT_SETS", "REDACT_VERSION", "SETS", "Allowlist", "AllowlistError", "Hit",
           "LineHit", "PrivateNames", "SetInfo", "SetUnavailable", "chat_redact",
           "describe", "is_secret_path", "private_names", "redact", "scan", "scan_lines",
           "secret_path", "seen_through"]
