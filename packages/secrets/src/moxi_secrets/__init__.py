"""Moxi's secrets: named pattern sets and one way to look for them in text.

    from moxi_secrets import scan, redact

    hits = scan(text, ("credentials", "private-names"), path="docs/setup.md")
    clean = redact(text, ("credentials",))

The sets are "credentials" (gitleaks' pinned rules and a local list), "broad"
(catch-alls), "private-names" (the user's own list in secrets' config) and
"personal-data" (payment cards, IBANs, US SSNs, behind the `personal-data` extra). A
caller always names the sets it wants; secrets returns hits and redacted text and leaves
the action to the caller.

Standard library only at import.
"""

from .allow import Allowlist, AllowlistError
from .names import PrivateNames, private_names
from .paths import is_secret_path
from .scan import (SETS, Hit, LineHit, SetInfo, SetUnavailable, describe, redact, scan,
                   scan_lines)

__all__ = ["SETS", "Allowlist", "AllowlistError", "Hit", "LineHit", "PrivateNames",
           "SetInfo", "SetUnavailable", "describe", "is_secret_path", "private_names",
           "redact", "scan", "scan_lines"]
