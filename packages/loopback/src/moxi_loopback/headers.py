"""The header set every response carries, refusals and errors included.

The set is fixed: one Content-Security-Policy and five headers that never vary, plus
`Cache-Control` (`no-store`, or `no-cache` on assets). The policy is one of two named
variants: `page`, the one every module page runs under, and `gateway`, the same policy
with the two directives an installable web app needs. No caller writes a policy of its
own. A module server sends no
`Access-Control-*` header and no `Set-Cookie`; the only browser session is the shell's.
The shell adds any header of the set that a response lacks before the browser sees it.

The assembler refuses a CSP directive or a header value that could carry a second
header or a second directive: a header assembler that passed a separator or a newline
through would let whoever wrote the string inject headers.
"""

from __future__ import annotations

import re
import types
from collections.abc import Iterable, Sequence

# Only the names `moxi_loopback.api` re-exports are public; the rest are the package's own.
__all__ = [
    "complete",
    "response_headers",
]

#: `\A` and `\Z`, never `^` and `$`: Python's `$` matches before a trailing newline, so a
#: `$` pattern would admit `"default-src\n"` and the newline would ride onto the wire.
_DIRECTIVE = re.compile(r"\A[A-Za-z0-9-]+\Z")
#: An HTTP header name: RFC 9110's token characters.
_HEADER_NAME = re.compile(r"\A[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_CSP_FORBIDDEN = (";", ",", "\r", "\n", "\0")
_VALUE_FORBIDDEN = ("\r", "\n", "\0")

NO_STORE = "no-store"
NO_CACHE = "no-cache"

_DIRECTIVES = (
    ("default-src", "'none'"),
    ("script-src", "'self'"),
    ("style-src", "'self'"),
    ("img-src", "'self' data:"),
    ("font-src", "'self'"),
    ("connect-src", "'self'"),
    ("base-uri", "'none'"),
    ("form-action", "'self'"),
    ("frame-ancestors", "'none'"),
    ("require-trusted-types-for", "'script'"),
)


def csp(directives: Sequence[tuple[str, str]]) -> str:
    """Join `(name, value)` directives into one policy, in the order given.

    A directive with an empty value is emitted bare. Raises `ValueError` on a name that
    is not letters, digits and hyphens, a repeated name, or a value holding `;`, `,`, a
    CR, an LF or a NUL.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for name, value in directives:
        if not isinstance(name, str) or not _DIRECTIVE.match(name):
            raise ValueError(f"not a CSP directive name: {name!r}")
        if name.lower() in seen:
            raise ValueError(f"CSP directive {name!r} is given twice")
        seen.add(name.lower())
        if not isinstance(value, str) or any(bad in value for bad in _CSP_FORBIDDEN):
            raise ValueError(f"CSP value for {name!r} holds a separator or a newline")
        if not value.isascii():
            raise ValueError(f"CSP value for {name!r} is not ASCII")
        parts.append(f"{name} {value}".strip())
    if not parts:
        raise ValueError("a CSP needs at least one directive")
    return "; ".join(parts)


#: The one policy every module page runs under.
CSP = csp(_DIRECTIVES)

#: The policy of the phone's listener: the page policy, plus a manifest and a service
#: worker from the origin itself, so the app can be installed and its worker registered.
GATEWAY_CSP = csp((*_DIRECTIVES, ("manifest-src", "'self'"), ("worker-src", "'self'")))

#: The named policies `response_headers` takes.
POLICIES = types.MappingProxyType({"page": CSP, "gateway": GATEWAY_CSP})

#: The headers that never vary, in the order they are sent. `Cache-Control` is added
#: beside them per response.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), usb=(), payment=()"),
)

_SET_NAMES = frozenset(name.lower() for name, _ in SECURITY_HEADERS) | {"cache-control"}


def _encoded(name: str, value: str) -> tuple[bytes, bytes]:
    if not isinstance(name, str) or not _HEADER_NAME.match(name):
        raise ValueError(f"not a header name: {name!r}")
    if not isinstance(value, str) or any(bad in value for bad in _VALUE_FORBIDDEN):
        raise ValueError(f"the value of {name} holds a newline or a NUL")
    if not value.isascii():
        raise ValueError(f"the value of {name} is not ASCII")
    return name.lower().encode("ascii"), value.encode("ascii")


def response_headers(
    *, cache: str = NO_STORE, extra: Iterable[tuple[str, str]] = (), policy: str = "page"
) -> list[tuple[bytes, bytes]]:
    """The full set for one response, as ASGI header pairs, then `extra`.

    `cache` is `no-store`, or `no-cache` for an asset. `extra` holds the response's own
    headers (`Content-Type`, `ETag`, `Retry-After`, `Connection`). `policy` names the
    Content-Security-Policy: `page`, the default, or `gateway`, which adds `manifest-src
    'self'` and `worker-src 'self'` and nothing else. Raises `ValueError` for another
    policy name, an extra header that the set already holds (a Content-Security-Policy
    included), a `Set-Cookie` or `Access-Control-*` header, a name that is not a header
    name, or a value holding a CR, an LF or a NUL.
    """
    if cache not in (NO_STORE, NO_CACHE):
        raise ValueError(f"cache must be {NO_STORE!r} or {NO_CACHE!r}, not {cache!r}")
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ValueError(f"policy must be one of {sorted(POLICIES)}, not {policy!r}")
    out = [_encoded(name, POLICIES[policy] if name == "Content-Security-Policy" else value)
           for name, value in SECURITY_HEADERS]
    out.append((b"cache-control", cache.encode("ascii")))
    for name, value in extra:
        pair = _encoded(name, value)
        lowered = pair[0].decode("ascii")
        if lowered in _SET_NAMES:
            raise ValueError(f"{name} is part of the fixed header set")
        if lowered == "set-cookie" or lowered.startswith("access-control-"):
            raise ValueError(f"a page server never sends {name}")
        out.append(pair)
    return out


def complete(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """`headers` with every header of the set it lacks added at the end.

    Names compare without regard to case. A missing `Cache-Control` is added as
    `no-store`. Headers already present are kept as they are.
    """
    out = list(headers)
    present = {name.lower() for name, _ in out}
    for name, value in SECURITY_HEADERS:
        key = name.lower().encode("ascii")
        if key not in present:
            out.append((key, value.encode("ascii")))
    if b"cache-control" not in present:
        out.append((b"cache-control", NO_STORE.encode("ascii")))
    return out
